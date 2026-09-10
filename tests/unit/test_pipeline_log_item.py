"""log_item writes only where api-obj-log points — never to an implicit default.

The dump is a few hundred MB on a full run. Cloud Run's filesystem is in-memory
and counts against the task's memory limit, so an unconfigured fallback would
silently eat the budget of every scheduled run.
"""

import json
from unittest.mock import MagicMock

import pytest

from bawue.pipeline import SitzungsScraper, VorgangsScraper

SCRAPERS = [VorgangsScraper, SitzungsScraper]


def _scraper(api_obj_log):
    scraper = MagicMock()
    scraper.config.api_obj_log = api_obj_log
    scraper.scraper_id = "collector-1"
    return scraper


@pytest.mark.parametrize("cls", SCRAPERS)
@pytest.mark.parametrize("api_obj_log", [None, ""])
def test_no_log_dir_means_no_write(cls, api_obj_log, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    cls.log_item(_scraper(api_obj_log), {"titel": "x"})

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("cls", SCRAPERS)
def test_writes_to_the_configured_dir(cls, tmp_path):
    target = tmp_path / "dumps"

    cls.log_item(_scraper(str(target)), {"titel": "x"})

    written = (target / "collector-1.jsonl").read_text()
    assert json.loads(written.rstrip(",\n"))["titel"] == "x"
